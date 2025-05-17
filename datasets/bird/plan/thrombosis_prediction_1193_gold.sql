{
  "relOp": "LogicalAggregate",
  "keys": [
    {
      "column": 0,
      "type": "INTEGER"
    }
  ],
  "aggs": [],
  "id": "5",
  "inputs": [
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
              },
              {
                "kind": "LESS_THAN",
                "operator": "<",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 18,
                    "name": "$18",
                    "type": "FLOAT"
                  },
                  {
                    "kind": "LITERAL",
                    "value": 2.0,
                    "type": "DECIMAL",
                    "nullable": false,
                    "precision": 2
                  }
                ]
              },
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
                                "index": 8,
                                "name": "$8",
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
                    "value": "1991",
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
                    "kind": "OTHER_FUNCTION",
                    "operator": "STRFTIME",
                    "type": "VARCHAR",
                    "operands": [
                      {
                        "kind": "LITERAL",
                        "value": "%m",
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
                                "index": 8,
                                "name": "$8",
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
                    "value": "10",
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
                    "index": 0,
                    "name": "$0",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 7,
                    "name": "$7",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "Patient",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "Laboratory",
                  "id": "1",
                  "inputs": []
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}