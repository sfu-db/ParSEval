{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 11,
      "name": "$11",
      "type": "FLOAT"
    }
  ],
  "id": "4",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "AND",
        "operator": "AND",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "GREATER_THAN",
            "operator": ">",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "DIVIDE",
                "operator": "/",
                "type": "FLOAT",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 8,
                    "name": "$8",
                    "type": "FLOAT"
                  },
                  {
                    "kind": "CASE",
                    "operator": "CASE",
                    "type": "INTEGER",
                    "operands": [
                      {
                        "kind": "EQUALS",
                        "operator": "=",
                        "type": "BOOLEAN",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 7,
                            "name": "$7",
                            "type": "INTEGER"
                          },
                          {
                            "kind": "LITERAL",
                            "value": 0,
                            "type": "INTEGER",
                            "nullable": false,
                            "precision": 10
                          }
                        ]
                      },
                      {
                        "kind": "LITERAL",
                        "value": "NULL",
                        "type": "INTEGER",
                        "nullable": true,
                        "precision": 10
                      },
                      {
                        "kind": "INPUT_REF",
                        "index": 7,
                        "name": "$7",
                        "type": "INTEGER"
                      }
                    ]
                  }
                ]
              },
              {
                "kind": "LITERAL",
                "value": 29.0,
                "type": "DECIMAL",
                "nullable": false,
                "precision": 4
              }
            ]
          },
          {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 6,
                "name": "$6",
                "type": "INTEGER"
              },
              {
                "kind": "LITERAL",
                "value": 5,
                "type": "INTEGER",
                "nullable": false,
                "precision": 10
              }
            ]
          },
          {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 10,
                "name": "$10",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "201208",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
              }
            ]
          }
        ]
      },
      "variableset": "[]",
      "id": "3",
      "inputs": [
        {
          "relOp": "LogicalJoin",
          "joinType": "inner",
          "condition": {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 3,
                "name": "$3",
                "type": "INTEGER"
              },
              {
                "kind": "INPUT_REF",
                "index": 9,
                "name": "$9",
                "type": "INTEGER"
              }
            ]
          },
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "transactions_1k",
              "id": "0",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "yearmonth",
              "id": "1",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}