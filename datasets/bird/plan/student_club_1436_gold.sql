{
  "relOp": "LogicalAggregate",
  "keys": [
    {
      "column": 0,
      "type": "VARCHAR"
    }
  ],
  "aggs": [],
  "id": "3",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 1,
          "name": "$1",
          "type": "VARCHAR"
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
                    "kind": "INPUT_REF",
                    "index": 3,
                    "name": "$3",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "LITERAL",
                    "value": "Game",
                    "type": "VARCHAR",
                    "nullable": false,
                    "precision": -1
                  }
                ]
              },
              {
                "kind": "GREATER_THAN_OR_EQUAL",
                "operator": ">=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "OTHER_FUNCTION",
                    "operator": "UDATE",
                    "type": "DATE",
                    "operands": [
                      {
                        "kind": "CAST",
                        "operator": "CAST",
                        "type": "TIMESTAMP",
                        "operands": [
                          {
                            "kind": "OTHER_FUNCTION",
                            "operator": "SUBSTR",
                            "type": "VARCHAR",
                            "operands": [
                              {
                                "kind": "INPUT_REF",
                                "index": 2,
                                "name": "$2",
                                "type": "VARCHAR"
                              },
                              {
                                "kind": "LITERAL",
                                "value": 1,
                                "type": "INTEGER",
                                "nullable": false,
                                "precision": 10
                              },
                              {
                                "kind": "LITERAL",
                                "value": 10,
                                "type": "INTEGER",
                                "nullable": false,
                                "precision": 10
                              }
                            ]
                          }
                        ]
                      }
                    ]
                  },
                  {
                    "kind": "CAST",
                    "operator": "CAST",
                    "type": "DATE",
                    "operands": [
                      {
                        "kind": "LITERAL",
                        "value": "2019-03-15",
                        "type": "CHAR",
                        "nullable": false,
                        "precision": 10
                      }
                    ]
                  }
                ]
              },
              {
                "kind": "LESS_THAN_OR_EQUAL",
                "operator": "<=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "OTHER_FUNCTION",
                    "operator": "UDATE",
                    "type": "DATE",
                    "operands": [
                      {
                        "kind": "CAST",
                        "operator": "CAST",
                        "type": "TIMESTAMP",
                        "operands": [
                          {
                            "kind": "OTHER_FUNCTION",
                            "operator": "SUBSTR",
                            "type": "VARCHAR",
                            "operands": [
                              {
                                "kind": "INPUT_REF",
                                "index": 2,
                                "name": "$2",
                                "type": "VARCHAR"
                              },
                              {
                                "kind": "LITERAL",
                                "value": 1,
                                "type": "INTEGER",
                                "nullable": false,
                                "precision": 10
                              },
                              {
                                "kind": "LITERAL",
                                "value": 10,
                                "type": "INTEGER",
                                "nullable": false,
                                "precision": 10
                              }
                            ]
                          }
                        ]
                      }
                    ]
                  },
                  {
                    "kind": "CAST",
                    "operator": "CAST",
                    "type": "DATE",
                    "operands": [
                      {
                        "kind": "LITERAL",
                        "value": "2020-03-20",
                        "type": "CHAR",
                        "nullable": false,
                        "precision": 10
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
                    "index": 6,
                    "name": "$6",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "LITERAL",
                    "value": "Closed",
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
              "table": "event",
              "id": "0",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}