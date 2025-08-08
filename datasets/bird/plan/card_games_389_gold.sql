{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 0,
      "name": "$0",
      "type": "INTEGER"
    },
    {
      "kind": "INPUT_REF",
      "index": 1,
      "name": "$1",
      "type": "VARCHAR"
    }
  ],
  "id": "5",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "AND",
        "operator": "AND",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 0,
                "name": "$0",
                "type": "INTEGER"
              },
              {
                "kind": "SCALAR_QUERY",
                "operator": "$SCALAR_QUERY",
                "operands": [],
                "query": [
                  {
                    "relOp": "LogicalProject",
                    "project": [
                      {
                        "kind": "INPUT_REF",
                        "index": 0,
                        "name": "$0",
                        "type": "INTEGER"
                      }
                    ],
                    "id": "2",
                    "inputs": [
                      {
                        "relOp": "LogicalFilter",
                        "condition": {
                          "kind": "EQUALS",
                          "operator": "=",
                          "type": "BOOLEAN",
                          "operands": [
                            {
                              "kind": "INPUT_REF",
                              "index": 10,
                              "name": "$10",
                              "type": "FLOAT"
                            },
                            {
                              "kind": "LITERAL",
                              "value": 5.0,
                              "type": "FLOAT",
                              "nullable": false,
                              "precision": 15
                            }
                          ]
                        },
                        "variableset": "[]",
                        "id": "1",
                        "inputs": [
                          {
                            "relOp": "LogicalTableScan",
                            "table": "cards",
                            "id": "0",
                            "inputs": []
                          }
                        ]
                      }
                    ]
                  }
                ]
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
                "index": 2,
                "name": "$2",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "10E",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
              }
            ]
          }
        ]
      },
      "variableset": "[]",
      "id": "4",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "set_translations",
          "id": "3",
          "inputs": []
        }
      ]
    }
  ]
}