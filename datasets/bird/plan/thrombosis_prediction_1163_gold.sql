{
  "relOp": "LogicalAggregate",
  "keys": [],
  "aggs": [
    {
      "operator": "COUNT",
      "distinct": false,
      "ignoreNulls": false,
      "operands": [],
      "type": "BIGINT",
      "name": "EXPR$0"
    }
  ],
  "id": "3",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "LITERAL",
          "value": 0,
          "type": "INTEGER",
          "nullable": false,
          "precision": 10
        }
      ],
      "id": "2",
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
                    "kind": "OTHER_FUNCTION",
                    "operator": "STRFTIME",
                    "type": "VARCHAR",
                    "operands": [
                      {
                        "kind": "LITERAL",
                        "value": "%Y",
                        "type": "CHAR",
                        "nullable": false,
                        "precision": 2
                      },
                      {
                        "kind": "CAST",
                        "operator": "CAST",
                        "type": "VARCHAR",
                        "operands": [
                          {
                            "kind": "CAST",
                            "operator": "CAST",
                            "type": "TIMESTAMP",
                            "operands": [
                              {
                                "kind": "INPUT_REF",
                                "index": 3,
                                "name": "$3",
                                "type": "DATE"
                              }
                            ]
                          }
                        ]
                      }
                    ]
                  },
                  {
                    "kind": "LITERAL",
                    "value": "1997",
                    "type": "VARCHAR",
                    "nullable": false,
                    "precision": -1
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
                    "index": 1,
                    "name": "$1",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "LITERAL",
                    "value": "F",
                    "type": "VARCHAR",
                    "nullable": false,
                    "precision": -1
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
                    "index": 5,
                    "name": "$5",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "LITERAL",
                    "value": "-",
                    "type": "VARCHAR",
                    "nullable": false,
                    "precision": -1
                  }
                ]
              }
            ]
          },
          "variableset": "[]",
          "id": "1",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "Patient",
              "id": "0",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}